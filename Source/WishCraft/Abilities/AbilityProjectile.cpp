#include "Abilities/AbilityProjectile.h"

#include "Abilities/AbilityTypes.h"
#include "Abilities/HealthComponent.h"
#include "Components/SphereComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Components/PointLightComponent.h"
#include "GameFramework/ProjectileMovementComponent.h"
#include "Camera/CameraShakeBase.h"
#include "GameFramework/PlayerController.h"
#include "Kismet/GameplayStatics.h"
#include "Materials/MaterialInterface.h"
#include "Net/UnrealNetwork.h"
#include "NiagaraComponent.h"
#include "NiagaraFunctionLibrary.h"
#include "Sound/SoundBase.h"
#include "UObject/ConstructorHelpers.h"

AAbilityProjectile::AAbilityProjectile()
{
	PrimaryActorTick.bCanEverTick = false;
	bReplicates = true;
	SetReplicateMovement(true);

	CollisionComponent = CreateDefaultSubobject<USphereComponent>(TEXT("Collision"));
	CollisionComponent->InitSphereRadius(12.f);
	CollisionComponent->SetCollisionEnabled(ECollisionEnabled::QueryOnly);
	CollisionComponent->SetCollisionObjectType(ECC_WorldDynamic);
	CollisionComponent->SetCollisionResponseToAllChannels(ECR_Ignore);
	CollisionComponent->SetCollisionResponseToChannel(ECC_Pawn, ECR_Overlap);
	CollisionComponent->SetCollisionResponseToChannel(ECC_WorldStatic, ECR_Overlap);
	CollisionComponent->SetCollisionResponseToChannel(ECC_WorldDynamic, ECR_Overlap);
	CollisionComponent->SetGenerateOverlapEvents(true);
	CollisionComponent->OnComponentBeginOverlap.AddDynamic(this, &AAbilityProjectile::HandleOverlap);
	SetRootComponent(CollisionComponent);

	MeshComponent = CreateDefaultSubobject<UStaticMeshComponent>(TEXT("Mesh"));
	MeshComponent->SetupAttachment(CollisionComponent);
	MeshComponent->SetCollisionEnabled(ECollisionEnabled::NoCollision);
	MeshComponent->SetRelativeScale3D(FVector(0.3f));

	static ConstructorHelpers::FObjectFinder<UStaticMesh> SphereMesh(TEXT("/Engine/BasicShapes/Sphere.Sphere"));
	if (SphereMesh.Succeeded())
	{
		MeshComponent->SetStaticMesh(SphereMesh.Object);
	}

	// The trail rides the projectile, but its emitters are world-space: particles are left behind
	// where they spawned, so the trail streams instead of dragging along with the ball.
	TrailComponent = CreateDefaultSubobject<UNiagaraComponent>(TEXT("Trail"));
	TrailComponent->SetupAttachment(CollisionComponent);
	TrailComponent->bAutoActivate = false;

	// So the fireball actually lights the arena it flies through, which is most of why it reads as fire.
	GlowLight = CreateDefaultSubobject<UPointLightComponent>(TEXT("GlowLight"));
	GlowLight->SetupAttachment(CollisionComponent);
	GlowLight->SetMobility(EComponentMobility::Movable);
	GlowLight->SetIntensity(3200.f);
	GlowLight->SetAttenuationRadius(520.f);
	GlowLight->SetLightColor(FLinearColor(1.f, 0.42f, 0.1f));
	GlowLight->SetCastShadows(false);

	static ConstructorHelpers::FObjectFinder<UNiagaraSystem> TrailFinder(TEXT("/Game/Sandbox/VFX/NS_Fireball_Trail.NS_Fireball_Trail"));
	if (TrailFinder.Succeeded())
	{
		TrailEffect = TrailFinder.Object;
	}

	static ConstructorHelpers::FObjectFinder<UNiagaraSystem> ImpactFinder(TEXT("/Game/Sandbox/VFX/NS_Fireball_Impact.NS_Fireball_Impact"));
	if (ImpactFinder.Succeeded())
	{
		ImpactEffect = ImpactFinder.Object;
	}

	static ConstructorHelpers::FObjectFinder<USoundBase> ImpactAudioFinder(TEXT("/Game/Sandbox/Audio/SFX_Ability_Impact.SFX_Ability_Impact"));
	if (ImpactAudioFinder.Succeeded())
	{
		ImpactSound = ImpactAudioFinder.Object;
	}

	static ConstructorHelpers::FObjectFinder<UMaterialInterface> BodyMaterialFinder(TEXT("/Game/Sandbox/Materials/M_Fireball_Body.M_Fireball_Body"));
	if (BodyMaterialFinder.Succeeded())
	{
		BodyMaterial = BodyMaterialFinder.Object;
	}

	ProjectileMovement = CreateDefaultSubobject<UProjectileMovementComponent>(TEXT("ProjectileMovement"));
	ProjectileMovement->InitialSpeed = 2500.f;
	ProjectileMovement->MaxSpeed = 2500.f;
	ProjectileMovement->ProjectileGravityScale = 0.f;
	ProjectileMovement->bRotationFollowsVelocity = true;
	ProjectileMovement->bShouldBounce = false;
}

void AAbilityProjectile::BeginPlay()
{
	Super::BeginPlay();

	// Self-lit emissive body, so the ball is a light source rather than a grey sphere catching the sun.
	if (!BodyMaterial)
	{
		BodyMaterial = LoadObject<UMaterialInterface>(nullptr, TEXT("/Game/Sandbox/Materials/M_Fireball_Body.M_Fireball_Body"));
	}

	if (BodyMaterial)
	{
		MeshComponent->SetMaterial(0, BodyMaterial);
	}

	if (TrailComponent)
	{
		if (!TrailEffect)
		{
			TrailEffect = LoadObject<UNiagaraSystem>(nullptr, TEXT("/Game/Sandbox/VFX/NS_Fireball_Trail.NS_Fireball_Trail"));
		}

		TrailComponent->SetAsset(TrailEffect);
		TrailComponent->Activate();
	}
}

void AAbilityProjectile::GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const
{
	Super::GetLifetimeReplicatedProps(OutLifetimeProps);
	DOREPLIFETIME(AAbilityProjectile, InstigatorController);
}

void AAbilityProjectile::LaunchProjectile(AController* InInstigatorController, float InSpeed, float InDamage)
{
	APawn* OwningPawn = InInstigatorController ? InInstigatorController->GetPawn() : nullptr;
	InstigatorController = InInstigatorController;
	Damage = InDamage;

	SetOwner(OwningPawn);
	SetInstigator(OwningPawn);

	ProjectileMovement->InitialSpeed = InSpeed;
	ProjectileMovement->MaxSpeed = InSpeed;
	ProjectileMovement->Velocity = GetActorForwardVector() * InSpeed;

	SetLifeSpan(LifeSeconds);

	UE_LOG(LogSandbox, Log, TEXT("Projectile launched from %s (speed %.0f, damage %.0f)"),
		*GetActorLocation().ToCompactString(), InSpeed, InDamage);
}

void AAbilityProjectile::HandleOverlap(UPrimitiveComponent* OverlappedComponent, AActor* OtherActor, UPrimitiveComponent* OtherComp, int32 OtherBodyIndex, bool bFromSweep, const FHitResult& SweepResult)
{
	if (!HasAuthority() || !OtherActor || OtherActor == this)
	{
		return;
	}

	if (const APawn* OwningPawn = Cast<APawn>(GetOwner()))
	{
		if (OtherActor == OwningPawn)
		{
			return;
		}
	}

	Explode(OtherActor);
}

void AAbilityProjectile::Explode(AActor* DirectHitActor)
{
	if (!HasAuthority())
	{
		return;
	}

	if (DirectHitActor)
	{
		if (UHealthComponent* TargetHealth = DirectHitActor->FindComponentByClass<UHealthComponent>())
		{
			TargetHealth->ApplyDamage(Damage, GetInstigator());
		}
	}

	MulticastExplode();
	Destroy();
}

void AAbilityProjectile::MulticastExplode_Implementation()
{
	const FVector ImpactLocation = GetActorLocation();
	const FRotator ImpactRotation = GetActorRotation();

	MeshComponent->SetVisibility(false, true);
	CollisionComponent->SetCollisionEnabled(ECollisionEnabled::NoCollision);

	if (TrailComponent)
	{
		TrailComponent->Deactivate();
	}

	if (GlowLight)
	{
		GlowLight->SetVisibility(false);
	}

	if (!ImpactEffect)
	{
		ImpactEffect = LoadObject<UNiagaraSystem>(nullptr, TEXT("/Game/Sandbox/VFX/NS_Fireball_Impact.NS_Fireball_Impact"));
	}

	if (ImpactEffect)
	{
		UNiagaraFunctionLibrary::SpawnSystemAtLocation(this, ImpactEffect, ImpactLocation, ImpactRotation,
			FVector(1.f), /*bAutoDestroy*/ true);
	}

	if (!ImpactSound)
	{
		ImpactSound = LoadObject<USoundBase>(nullptr, TEXT("/Game/Sandbox/Audio/SFX_Ability_Impact.SFX_Ability_Impact"));
	}

	if (ImpactSound)
	{
		UGameplayStatics::PlaySoundAtLocation(this, ImpactSound, ImpactLocation);
	}

	// A hit you can feel. Local controllers only: a remote client's view is its own business.
	static TWeakObjectPtr<UClass> CachedShakeClass;
	if (!CachedShakeClass.IsValid())
	{
		CachedShakeClass = LoadClass<UCameraShakeBase>(nullptr, TEXT("/Game/Sandbox/VFX/CS_Impact.CS_Impact_C"));
	}

	if (UClass* ShakeClass = CachedShakeClass.Get())
	{
		if (UWorld* World = GetWorld())
		{
			for (FConstPlayerControllerIterator It = World->GetPlayerControllerIterator(); It; ++It)
			{
				if (APlayerController* PlayerController = It->Get())
				{
					if (PlayerController->IsLocalController())
					{
						PlayerController->ClientStartCameraShake(ShakeClass, 1.f);
					}
				}
			}
		}
	}

	UE_LOG(LogSandbox, Verbose, TEXT("Projectile exploded"));
}
