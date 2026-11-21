#include "Abilities/AbilityComponentBase.h"

#include "Abilities/AbilityLoadoutManager.h"
#include "GameFramework/Pawn.h"

DEFINE_LOG_CATEGORY(LogSandbox);

UAbilityComponentBase::UAbilityComponentBase()
{
	PrimaryComponentTick.bCanEverTick = false;
	// Derived state on every machine - never replicate these components.
	SetIsReplicatedByDefault(false);
}

void UAbilityComponentBase::ConfigureAbility(FName InToken, const FAbilityTokenDefinition& Definition, const TMap<FName, float>& InParams, UAbilityLoadoutManager* InManager, bool bInHasAuthority)
{
	Token = InToken;
	Category = Definition.Category;
	Params = InParams;
	LoadoutManager = InManager;
	bHasAuthority = bInHasAuthority;

	UE_LOG(LogSandbox, Log, TEXT("Ability %s configured on %s (authority: %s)"),
		*Token.ToString(),
		*GetNameSafe(GetOwner()),
		bHasAuthority ? TEXT("yes") : TEXT("no"));
}

float UAbilityComponentBase::GetParam(FName ParamName, float Fallback) const
{
	const float* Found = Params.Find(ParamName);
	return Found ? *Found : Fallback;
}

APawn* UAbilityComponentBase::GetAvatarPawn() const
{
	return Cast<APawn>(GetOwner());
}

float UAbilityComponentBase::GetCooldownRemaining() const
{
	return LoadoutManager ? LoadoutManager->GetCooldownRemaining(Token) : 0.f;
}

float UAbilityComponentBase::BeginEffect()
{
	UE_LOG(LogSandbox, Log, TEXT("Ability %s began"), *Token.ToString());
	return 0.f;
}

void UAbilityComponentBase::EndEffect()
{
	UE_LOG(LogSandbox, Log, TEXT("Ability %s ended"), *Token.ToString());
}

bool UAbilityComponentBase::CanActivate() const
{
	return GetCooldownRemaining() <= 0.f;
}

void UAbilityComponentBase::HandleCosmetic(bool bStarting)
{
	BP_OnCosmetic(bStarting);
}
